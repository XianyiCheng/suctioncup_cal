#!/usr/bin/env python

#import numpy as np
from scipy.optimize import minimize
from utils import *
import tf.transformations as tr
from sklearn.neighbors import NearestNeighbors
from sklearn.linear_model import LinearRegression
import numpy as np
import json
from sympy import *

import rospy
from robot_comm.msg import robot_CartesianLog
from robot_comm.srv import *
import random
import math
import rosbag
import time
import csv
from std_msgs.msg import *
from geometry_msgs.msg import *
from threading import Lock


class compliantMapping():
    # TODO: verify the accuracy of mapping
    def __init__(self, mapping_file):

        K_neighbor_wrench = 15
        K_neighbor_pos = 60
        self.alpha_wrench =[1,1,1,0.1,0.1,0.1]
        self.alpha_pos = [1,1,1,50,50,50]

        with open(mapping_file) as f:
            json_data = json.load(f)
        self.ref_cartesians = np.array(json_data["ref_cartesians"]).T
        self.ref_wrenches = np.array(json_data["ref_wrenches"]).T
        self.ref_contact_wrenches = np.array(json_data["ref_contact_wrenches"]).T
        #print(self.ref_cartesians)
        self.ref_poses = toExpmap(self.ref_cartesians)

        self.contact_wrench_nbrs = NearestNeighbors(n_neighbors=K_neighbor_wrench, algorithm='ball_tree').fit(\
        self.ref_contact_wrenches*self.alpha_wrench)

        self.wrench_nbrs = NearestNeighbors(n_neighbors=K_neighbor_wrench, algorithm='ball_tree').fit(\
        self.ref_wrenches*self.alpha_wrench)

        self.pos_nbrs = NearestNeighbors(n_neighbors=K_neighbor_pos, algorithm='ball_tree').fit(\
        self.ref_poses*self.alpha_pos)

        self.N_sample = self.ref_cartesians.shape[0]

    def __KNNmapping_sum(self, X, Xref_nbrs, X_ref, Y_ref, alpha):
        # use weighted sum to calculate query point
        # X -> Y all R6->R6
        X_ = X*alpha
        N = X.shape[0]
        Y = np.zeros([N,6])

        distances, indices = Xref_nbrs.kneighbors(X_)

        exact_ = distances==0
        weights = np.zeros(distances.shape)
        for i in range(N):
            if True in exact_[i]:
                weights[i][exact_[i]] = 1
                weights[i][exact_[i]==False]=0
            else:
                weights[i] = 1/distances[i]
        weights = (weights.T/np.sum(weights,axis=1)).T

        for i in range(N):
            ind = indices[i]
            weight_i = weights[i]
            Y_i = Y_ref[ind]
            Y[i] = np.sum((Y_i.T*weight_i).T,axis=0)
        return Y

    def __KNNmapping_lr(self, X, Xref_nbrs, X_ref, Y_ref, alpha):
        # use linear regression to calculate query point
        # X -> Y all R6->R6
        X_ = X*alpha
        distances, indices = Xref_nbrs.kneighbors(X_)
        X_ind = np.squeeze(X_ref[indices])
        Y_ind = np.squeeze(Y_ref[indices])
        reg = LinearRegression().fit(X_ref, Y_ref)
        Y = reg.predict(X)
        return Y

    def __get_param(self, X_name, Y_name):
        if X_name == 'wrench':
            _Xref_nbrs = self.wrench_nbrs
            _Xref = self.ref_wrenches
            _alpha = self.alpha_wrench
        elif X_name == 'contact_wrench':
            _Xref_nbrs = self.contact_wrench_nbrs
            _Xref = self.ref_contact_wrenches
            _alpha = self.alpha_wrench
        elif X_name == 'config':
            _Xref_nbrs = self.pos_nbrs
            _Xref = self.ref_poses
            _alpha = self.alpha_pos
        else:
            raise NameError('the 1st param should only be wrench, contact_wrench or config')

        if Y_name == 'wrench':
            _Yref = self.ref_wrenches
        elif Y_name == 'contact_wrench':
            _Yref = self.ref_contact_wrenches
        elif Y_name == 'config':
            _Yref = self.ref_poses
        else:
            raise NameError('the 2nd param should only be wrench, contact_wrench or config')

        return _Xref, _Xref_nbrs, _Yref, _alpha

    def local_lr(self, X, X_name, Y_name):

        Xref, Xref_nbrs, Yref, alpha = self.__get_param(X_name,Y_name)
        X_ = X*alpha
        distances, indices = Xref_nbrs.kneighbors(X_)
        X_ind = np.squeeze(Xref[indices])
        Y_ind = np.squeeze(Yref[indices])
        reg = LinearRegression().fit(X_ind, Y_ind)
        return reg

    def local_lr_possemidef(self, X, X_name, Y_name):
        # return: Y.T = K*(X-x0).T + y0.T
        Xref, Xref_nbrs, Yref, alpha = self.__get_param(X_name,Y_name)
        X_ = X*alpha
        distances, indices = Xref_nbrs.kneighbors(X_)
        itself = np.argwhere(distances==0)
        if len(itself)!=0:
            indices = np.delete(indices,np.argwhere(distances==0)[0])
        X_ind = np.squeeze(Xref[indices])
        Y_ind = np.squeeze(Yref[indices])

        x0 = np.mean(X_ind, axis=0)
        y0 = np.mean(Y_ind, axis=0)
        K_T = posdef_estimation(X_ind[:, :-1] - x0[:-1],Y_ind[:,:-1]- y0[:-1])
        K = np.zeros([6,6])
        K[0:5,0:5] = K_T.T
        return K,x0,y0

    def Kfx(self,x_pos):
        K, x0,y0 = self.local_lr_possemidef(x_pos, 'config', 'wrench')
        return K
        #return self.local_lr(x_pos,'config','wrench').coef_

    def Mapping(self, X, X_name, Y_name):
        if X_name == 'wrench':
            _Xref_nbrs = self.wrench_nbrs
            _Xref = self.ref_wrenches
            _alpha = self.alpha_wrench
        elif X_name == 'contact_wrench':
            _Xref_nbrs = self.contact_wrench_nbrs
            _Xref = self.ref_contact_wrenches
            _alpha = self.alpha_wrench
        elif X_name == 'config':
            _Xref_nbrs = self.pos_nbrs
            _Xref = self.ref_poses
            _alpha = self.alpha_pos
        else:
            raise NameError('the 1st param should only be wrench, contact_wrench or config')

        if Y_name == 'wrench':
            _Y_ref = self.ref_wrenches
        elif Y_name == 'contact_wrench':
            _Y_ref = self.ref_contact_wrenches
        elif Y_name == 'config':
            _Y_ref = self.ref_poses
        else:
            raise NameError('the 2nd param should only be wrench, contact_wrench or config')

        return self.__KNNmapping_sum(X, _Xref_nbrs,_Xref, _Y_ref, _alpha)

class abbRobot():

    def __init__(self, *bagname_argv):

        self.nodename= 'optimal_control'
        self.robot_ns = "/abb120"
        self.lock = Lock()
        self.cur_wrenches = np.zeros([10,6])

        if len(bagname_argv)>0:
            bagname = bagname_argv[0]
            print("Recording bag with name: {0}".format(bagname))
            self.bag = rosbag.Bag(bagname, mode='w')

    def initialize(self):
        rospy.loginfo('Waiting for services...')
        rospy.init_node(self.nodename, anonymous=True, disable_signals=True)
        rospy.wait_for_service(self.robot_ns + '/robot_SetWorkObject')

        self.SetSpeed = rospy.ServiceProxy(self.robot_ns + '/robot_SetSpeed', robot_SetSpeed)
        self.SetCartesian = rospy.ServiceProxy(self.robot_ns + '/robot_SetCartesian', robot_SetCartesian)
        self.GetCartesian = rospy.ServiceProxy(self.robot_ns + '/robot_GetCartesian', robot_GetCartesian)
        self.netft_subscriber = rospy.Subscriber('/netft/data', WrenchStamped, self.force_torque_callback)
        rospy.loginfo('All services registered.')

        p = self.get_current_cartesian()
        print('initial cartesian: ')
        print(p)
        self.SetCartesian(*p)
        print('Robot speed set to slow.')
        self.SetSpeed(10, 10)

    def go(self, p):
        print('robot go to: '+ str(p))
        self.SetCartesian(*p)

    def force_torque_callback(self,data):
        with self.lock:
            new = np.array([data.wrench.force.x,data.wrench.force.y,data.wrench.force.z,data.wrench.torque.x, data.wrench.torque.y,data.wrench.torque.z])
            self.cur_wrenches[0:9] = self.cur_wrenches[1:10]
            self.cur_wrenches[-1] = new

    def current_ft(self):
        with self.lock:
            w = np.mean(self.cur_wrenches, axis=0)
        return w

    def get_current_cartesian(self):
        pos = self.GetCartesian()
        p=[pos.x, pos.y, pos.z, pos.q0, pos.qx, pos.qy, pos.qz]
        return p

    def record_state_robot_ft(self,seqid, obj_state, f):

        self.record_ft(seqid, f)

        pos = self.GetCartesian()
        time_now = rospy.Time.now()
        rob_pos_data = to_poseStamped(time_now,seqid,[pos.x, pos.y, pos.z], [pos.q0, pos.qx, pos.qy, pos.qz])
        obj_pos_data = to_poseStamped(time_now,seqid,[obj_state[0], obj_state[1], obj_state[2]], [obj_state[3], obj_state[4], obj_state[5], obj_state[6]])

        # record the cartesian and wrench
        self.bag.write('robot position', rob_pos_data)
        self.bag.write('object position', obj_pos_data)
        print('Position Data recorded.')

    def record_ft(self,seqid, f):
        wrench_data = to_wrenchStamped(rospy.Time.now(),seqid,f)
        wrench_data.header.frame_id = 'contact_frame'
        self.bag.write('wrench', wrench_data)
        print('FT Data recorded.')


    def stop(self):
        if hasattr(self, 'bag'):
            self.bag.close()
            print('Task finished, stop recording.')
        self.netft_subscriber.unregister()

class taskModel():

    def __init__(self, mapping_file, task_file):

        with open(task_file) as f:
            task_data = json.load(f)
        # task and obj param
        self.obj_m = task_data["object_mass"]
        self.gravity = 9.8
        self.obj_lx = task_data["object_lx"]
        self.obj_ly = task_data["object_ly"]
        self.obj_lz = task_data["object_lz"]
        # contact
        self.pc = np.squeeze(np.array(task_data["finger_contact_position"]).T)#np.array([self.obj_lx*0.8,0,self.obj_lz]); # contact position relative to the object center
        self.Rc = np.array(task_data["finger_contact_rotm"])#np.array([[1,0,0],[0,-1,0],[0,0,-1]])# contact orientation
        self.qc = rotm2quat(self.Rc)
        self.fric_mu = task_data["fric_coef"]
        self.gripper_length=task_data["gripper_length"]

        # todo:import computed obj/contact/robot end traj/actual robot traj
        self.origin = np.array(task_data["origin"])
        self.ref_obj_traj=np.array(task_data["object_trajectory"]).T
        self.ref_contact_traj=np.array(task_data["contact_trajectory"]).T
        self.ref_end_traj=np.array(task_data["rigidend_trajectory"]).T
        self.ref_act_traj=np.array(task_data["actual_trajectory"]).T
        self.ref_force_traj=np.array(task_data["contact_force_traj"]).T
        self.total_timestep = self.ref_act_traj.shape[0]
        self.current_timestep=0

        # to record estimated/observed states during execution
        self.obj_traj=np.zeros([self.total_timestep,7])
        self.contact_traj=np.zeros([self.total_timestep,7]) # contact force!
        self.config_traj = np.zeros([self.total_timestep,7])
        self.end_traj=np.zeros([self.total_timestep,7])
        self.act_traj=np.zeros([self.total_timestep,7])
        self.force_traj=np.zeros([self.total_timestep,6])

        # env contact in the obj frame
        self.envc1 = np.squeeze(np.array(task_data["env_contact1"]).T)#[self.obj_lx,self.obj_ly,-self.obj_lz];
        self.envc2 = np.squeeze(np.array(task_data["env_contact2"]).T)#[self.obj_lx,-self.obj_ly,-self.obj_lz];
        self.envc1_w = self.ref_obj_traj[0,0:3] + np.dot(quat2rotm(self.ref_obj_traj[0,3:]),self.envc1)
        self.envc2_w = self.ref_obj_traj[0,0:3] + np.dot(quat2rotm(self.ref_obj_traj[0,3:]),self.envc2)

        # compliant mapping
        self.vc_mapping = compliantMapping(mapping_file)

    def wrench_compensation(self, raw_wrench, q_robot):
        # raw_wrench -> actual wrench applied on the right end
        #R: raw_wrench corresponding robot orientation
        R = quat2rotm(q_robot)
        G = [-0.0435, -0.0621, -1.4]
        P =  [0.000786, -0.00269, -0.0709]
        ft_offset = [ 1.03, 2.19, -2.28, 0.215, -0.0865, -0.0101]
        p_st =np.array([0, 0, 0.0962])
        #q_st =  [0.5556, 0, 0, 0.8315]; #qw: 0.5556 qx: 0 qy: 0 qz: 0.8315
        R_st = np.array([[-0.3827,-0.9239,0],[0.9239,-0.3827,0],[0,0,1.0000]])
        # sensor frame to tool frame
        Adg = adjointTrans(R_st,p_st)
        ft_t = np.dot(Adg.T,raw_wrench)

        F = np.dot(R.T,G)
        T = np.cross(P, F)
        ft_tool = ft_t + ft_offset - np.concatenate((F,T))
        return ft_tool

    def state_estimation(self, ft_force, robot_cartesian):
        i = self.current_timestep

        # observation
        self.act_traj[i] = robot_cartesian
        print(self.act_traj[i])
        self.end_traj[i] = self.actual2robot(robot_cartesian)
        print(self.end_traj[i])
        print(self.ref_end_traj[i])
        #print('current robot:',self.end_traj[i])
        #ft_force = self.wrench_compensation(robot_ft,robot_cartesian[3:]) TODO change this back!!

        # estimation
        x_config_guess = self.vc_mapping.Mapping(-ft_force.reshape(1,-1), 'wrench', 'config').reshape(-1)
        print('x_config_guess',x_config_guess)
        #print('x_config_guess', x_config_guess)

        # use x-config guess from mapping(inaccurate) and reference obj traj (assume small error) to estimate the true state
        x = self.state_model(np.zeros(6), self.end_traj[i], x_config_guess, ft_force, self.ref_obj_traj[i])

        dp_obj = x[0:3]
        dq_obj = x[3:7]/np.linalg.norm(x[3:7])
        dp_config=x[7:10]
        dq_config=x[10:14]
        dx_config = np.concatenate((dp_config, quat2exp(dq_config)))
        print(dx_config)

        self.obj_traj[i,0:3] = self.ref_obj_traj[i,0:3] + dp_obj
        self.obj_traj[i,3:] = quat_mul(dq_obj, self.ref_obj_traj[i,3:])
        print('estimated obj', self.obj_traj[i])

        # compute config
        self.contact_traj[i,0:3] = np.dot(quat2rotm(self.obj_traj[i,3:]),self.pc) + self.obj_traj[i,0:3]
        self.contact_traj[i,3:] = quat_mul(self.qc,self.obj_traj[i,3:])

        self.config_traj[i,0:3] = x_config_guess[0:3] + dp_config
        self.config_traj[i,3:] = quat_mul(dq_config, exp2quat(x_config_guess[3:]))
        R_config = quat2rotm(self.config_traj[i,3:])
        self.force_traj[i,:] = np.dot(adjointTrans(R_config.T, -np.dot(R_config.T,0.001*self.config_traj[i,0:3]).T),-ft_force)# adjoint trans

    def position_optimal_control(self, ft_force):
        # return x_robot
        print('OPTIMAL CONTROL')

        i = self.current_timestep
        x_obj_star = self.ref_obj_traj[i+1]
        ref_p_robot = self.ref_end_traj[i+1,0:3]
        ref_q_robot = self.ref_end_traj[i+1,3:]

        p_obj_star = x_obj_star[0:3]
        q_obj_star = x_obj_star[3:]

        x_robot_=self.end_traj[i]
        p_config_ = self.config_traj[i,0:3]
        R_config_ = quat2rotm(self.config_traj[i,3:])
        f_config_ = ft_force
        #x_obj_ = self.obj_traj[i]
        x_obj_ = x_obj_star

        p_robot_ = x_robot_[0:3]
        R_robot_ = quat2rotm(x_robot_[3:])
        p_obj_ = x_obj_[0:3]
        q_obj_ = x_obj_[3:]


        x_config_ = np.append(p_config_,rotm2exp(R_config_))
        K_config = self.vc_mapping.Kfx(x_config_.reshape(1, -1)) #force exert on the rigid end relative to rigid end pos change
        #K_config = -np.diag([10,10,10,200,200,200])
        K_spring = 2*K_config
        f_spring_ = -f_config_
        q_robot_ = rotm2quat(R_robot_)

        def eq_constraints(x):
            #temp let the p_obj as the goal
            dp_obj = x[0:3]
            dq_obj = x[3:7]/np.linalg.norm(x[3:7])
            p_robot=x[7:10]
            q_robot=x[10:14]/np.linalg.norm(x[10:14])

            # compute current states
            #p_robot = np.add(p_robot_, dp_robot)
            #q_robot = quat_mul(dq_robot,q_robot_)
            R_robot = quat2rotm(q_robot)
            p_obj = np.add(p_obj_, dp_obj)
            q_obj = quat_mul(dq_obj,q_obj_)
            R_obj = quat2rotm(q_obj)
            # compute config
            p_contact = np.dot(R_obj,self.pc)+p_obj
            R_contact = np.dot(self.Rc,R_obj)
            R_config = np.dot(R_contact.T, R_robot)
            p_config = np.dot(R_contact.T,p_robot - p_contact)
            x_config = np.append(p_config,rotm2exp(R_config))
            f_spring = np.dot(K_spring,x_config-x_config_) + f_spring_#self.vc_mapping.Mapping(x_config.reshape(1,-1), 'config', 'contact_wrench')
            f_contact = np.dot(adjointTrans(R_config.T, -np.dot(R_config.T,0.001*p_config)),f_spring)
            # goal_constraints: given robot position, temp
            eqn_goal_q = np.linalg.norm(np.dot(q_obj,q_obj_star) - 1)
            eqn_goal_p = np.linalg.norm(p_obj - p_obj_star)

            # env_constraints:

            # stick with env contact
            eqn_env1 = np.sum((np.dot(R_obj,self.envc1) + p_obj  - self.envc1_w)**2)
            eqn_env2 = np.sum((np.dot(R_obj,self.envc2) + p_obj  - self.envc2_w)**2)

            # quasi-static eqn
            f_co = np.dot(adjointTrans(self.Rc.T, -np.dot(self.Rc.T,0.001*self.pc)).T, f_contact.reshape(-1))
            G_o = np.concatenate((np.dot(R_obj.T,[0,0,-self.obj_m*self.gravity]),np.zeros(3)),axis = 0).T

            ADG_env = np.zeros([6,6])
            ADG_env[0:3,0:3] = R_obj.T
            ADG_env[0:3,3:] = R_obj.T
            ADG_env[3:,0:3] = np.dot(-skew_sym(0.001*self.envc1),R_obj.T)
            ADG_env[3:,3:] = np.dot(-skew_sym(0.001*self.envc2), R_obj.T)
            f_envo = np.dot(ADG_env,x[14:])
            #print('f_spring',f_spring)
            #print('f_contact',f_contact)
            #print('fco',f_co)
            #print('G',G_o)
            #print('fenv',f_envo)
            eqn_env_f = np.linalg.norm(f_co+G_o+f_envo)/100


            #eqns = [eqn_goal_p,eqn_goal_q,eqn_env1,eqn_env2]
            eqns = [eqn_env1, eqn_env2, eqn_env_f]
            return eqns

        def cost(x):
            dp_obj = x[0:3]
            dq_obj = x[3:7]/np.linalg.norm(x[3:7])
            p_robot=x[7:10]
            q_robot=x[10:14]/np.linalg.norm(x[10:14])
            p_obj = np.add(p_obj_, dp_obj)
            q_obj = quat_mul(dq_obj,q_obj_)
            '''
            # compute current states
            R_robot = quat2rotm(q_robot)
            R_obj = quat2rotm(q_obj)
            # compute config
            p_contact = np.dot(R_obj,self.pc)+p_obj
            R_contact = np.dot(self.Rc,R_obj)
            R_config = np.dot(R_contact.T, R_robot)
            p_config = np.dot(R_contact.T,p_robot - p_contact)
            x_config = np.append(p_config,rotm2exp(R_config))
            f_spring = np.dot(K_spring,x_config-x_config_) + f_spring_#self.vc_mapping.Mapping(x_config.reshape(1,-1), 'config', 'contact_wrench')
            energy = self.obj_m*self.gravity*dq_obj[2] + 0.5*np.dot(x_config-x_config_,K_spring).dot(x_config-x_config_) + np.dot(x_config-x_config_, f_spring)
            '''
            #c = np.dot(dp_obj,dp_obj) + 10*np.dot(p_robot-ref_p_robot,p_robot-ref_p_robot) + 50*np.arccos(min(1,abs(np.dot(q_robot,ref_q_robot))))+np.arccos(min(1,abs(np.dot(q_obj,q_obj_))))
            cref = 5*np.dot(p_robot-ref_p_robot,p_robot-ref_p_robot) + 50*np.arccos(min(1,abs(np.dot(q_robot,ref_q_robot))))
            cu = 5*np.dot(p_robot-x_robot_[0:3],p_robot-x_robot_[0:3]) + 50*np.arccos(min(1,abs(np.dot(q_robot,x_robot_[3:]))))
            eqn_goal_q = 5*np.linalg.norm(np.dot(q_obj,q_obj_star)-1)
            eqn_goal_p = np.linalg.norm(p_obj_+dp_obj - p_obj_star)
            #c = 3*cref+10*cu+20*(eqn_goal_p+eqn_goal_q)+10*(np.linalg.norm(x[14:16])+np.linalg.norm(x[17:19])-x[16]-x[19])
            c = 10*cref+10*cu+20*(eqn_goal_p+eqn_goal_q)+10*(np.linalg.norm(x[14:16])+np.linalg.norm(x[17:19])-x[16]-x[19])
            return c

        x0 = np.zeros(20)
        #x0[0:3] = np.random.rand(3)/100
        x0[3:7]= np.array([1,0,0,0])
        x0[7:14] = np.copy(x_robot_)
        x0[16] = self.obj_m*self.gravity*0.5
        x0[19] = self.obj_m*self.gravity*0.5
        #bnds = ((-10,10),(-10,10),(-20,20),(None,None),(None,None),(None,None),(None,None),(None,None),(None,None),(None,None),(None,None),(None,None),(None,None),(None,None),(None,None),(None,None),(None,None),(None,None),(None,None),(None,None))
        print('x0', x0)
        print('eq_constraints x0', eq_constraints(x0))
        cons = ({"type": "eq", "fun": eq_constraints},
                {"type": "ineq", "fun": lambda x: self.fric_mu * x[16] - np.linalg.norm(x[14:16])},
                {"type": "ineq", "fun": lambda x: self.fric_mu * x[19] - np.linalg.norm(x[17:19])},
                {"type": "ineq", "fun": lambda x:10-x[16]},
                {"type": "ineq", "fun": lambda x:10-x[19]},
                {"type": "ineq", "fun": lambda x:x[16]-5},
                {"type": "ineq", "fun": lambda x:x[19]-5},
                {"type": "eq", "fun": lambda x:  1 - np.dot(x[3:7],x[3:7])},
                {"type": "eq", "fun": lambda x:  1 - np.dot(x[10:14],x[10:14])})
        #res = minimize(energy, x0, method='SLSQP', tol=1e-4, jac=jac_energy, constraints=cons)
        opts = {'maxiter':500}
        res = minimize(cost, x0,tol=0.2, constraints=cons,  options=opts)#, bounds = bnds)
        print('status:',res.status,', iter:',res.nit, ', fun:',res.fun)
        print('x res', res.x)
        print('eq_constraints res', eq_constraints(res.x))
        dp_obj = res.x[0:3]
        dq_obj = res.x[3:7]/np.linalg.norm(res.x[3:7])
        p_obj = np.add(p_obj_, dp_obj)
        q_obj = quat_mul(dq_obj,q_obj_)
        eqn_goal_q = 5*np.linalg.norm(np.dot(q_obj,q_obj_star)-1)
        eqn_goal_p = np.linalg.norm(p_obj_+dp_obj - p_obj_star)
        if res.status != 0 or eqn_goal_q + eqn_goal_p >15 or res.fun > 12000:
            print('res eqn_goal p: ', eqn_goal_p),
            print('res eqn_goal q: ', eqn_goal_q),
            u = x0[7:14]
        else:
            u = res.x[7:14]
        return u

    def state_model(self,ut, x_robot_, x_config_, f_config_,x_obj_):
        # p_robot : the position of the rigid end
        # p_config: robot rigid end to the contact
        # return object position and contact force in the next timestep
        # pc: contact position relative to the object center
        # x_config: posistion + axis angle:  robot rigid end relative to the contact
        print('STATE ESTIMATION:')
        print('x_robot_', x_robot_)
        print('x_config_', x_config_)
        print('f_config_', f_config_)
        print('ref_obj_', x_obj_)

        p_robot_ = x_robot_[0:3]
        R_robot_ = quat2rotm(x_robot_[3:])
        p_obj_ = x_obj_[0:3]
        q_obj_ = x_obj_[3:]
        p_config_ = x_config_[0:3]
        R_config_ = exp2rotm(x_config_[3:])

        Rut= exp2rotm(ut[3:])
        p_robot = np.add(p_robot_, ut[0:3])
        #R_robot = np.dot(R_robot_,Rut)
        R_robot = np.dot(Rut,R_robot_)
        q_robot = rotm2quat(R_robot)
        q_config_ = rotm2quat(R_config_)

        K_config = self.vc_mapping.Kfx(x_config_.reshape(1, -1)) #force exert on the rigid end relative to rigid end pos change
        #K_config = -np.identity(6)*10
        K_spring = K_config
        f_spring = -f_config_
        #f_spring = np.ones(6)*0.1
        # solve for dp_obj, dq_obj, dp_config, dq_config
        print('K_spring', K_spring)

        def robot_constraints(x):
            dp_obj = x[0:3]
            dq_obj = x[3:7]/np.linalg.norm(x[3:7])
            dp_config=x[7:10]
            dq_config=x[10:14]/np.linalg.norm(x[10:14])
            q_middle = quat_mul(q_obj_,self.qc)
            #eqn_q = quat_mul(quat_mul(quat_mul(dq_obj,q_middle),dq_config),q_config_) - q_robot
            q_temp = quat_mul(quat_mul(quat_mul(dq_obj, q_middle), dq_config), q_config_)
            eqn_q = np.dot(q_temp,q_robot) - 1
            R_obj = quat2rotm(dq_obj)*quat2rotm(q_obj_)
            eqn_p = np.linalg.norm(p_obj_ + dp_obj + np.dot(R_obj,self.pc) + R_obj.dot(self.Rc.dot(p_config_ + dp_config)) - p_robot)
            return np.concatenate(([eqn_q], [eqn_p]))

        def jac_robot(x):
            dp_obj = x[0:3]
            dq_obj = x[3:7]/np.linalg.norm(x[3:7])
            dp_config=x[7:10]
            dq_config=x[10:14]

            q_middle = quat_mul(q_obj_,self.qc)

            jac_q_obj = np.zeros([4,7])
            jac_q_obj[:,3:] = quat_mul_derivative_right(quat_mul(q_middle,dq_config))
            jac_q_config = np.zeros([4,7])
            jac_q_config[:,3:] = quat_mul_derivative_left(quat_mul(dq_obj,q_middle))

            jac_p_obj = np.zeros([3,7])
            jac_p_obj[0:3,0:3] = np.identity(3)
            x = quat2rotm(q_obj_).dot(self.pc+self.Rc.dot(p_config_ + dp_config))
            jac_p_obj[0:3,3:] = np.array([[ 2*dq_obj[2]*x[2] - 2*dq_obj[3]*x[1], 2*dq_obj[2]*x[1] + 2*dq_obj[3]*x[2], 2*dq_obj[0]*x[2] + 2*dq_obj[1]*x[1] - 4*dq_obj[2]*x[0], 2*dq_obj[1]*x[2] - 2*dq_obj[0]*x[1] - 4*dq_obj[3]*x[0]],\
            [ 2*dq_obj[3]*x[0] - 2*dq_obj[1]*x[2], 2*dq_obj[2]*x[0] - 4*dq_obj[1]*x[1] - 2*dq_obj[0]*x[2], 2*dq_obj[1]*x[0] + 2*dq_obj[3]*x[2], 2*dq_obj[0]*x[0] + 2*dq_obj[2]*x[2] - 4*dq_obj[3]*x[1]],\
            [ 2*dq_obj[1]*x[1] - 2*dq_obj[2]*x[0], 2*dq_obj[0]*x[1] - 4*dq_obj[1]*x[2] + 2*dq_obj[3]*x[0], 2*dq_obj[3]*x[1] - 4*dq_obj[2]*x[2] - 2*dq_obj[0]*x[0], 2*dq_obj[1]*x[0] + 2*dq_obj[2]*x[1]]])

            jac_p_config = np.zeros([3,7])
            jac_p_config[0:3,0:3] = quat2rotm(dq_obj)*quat2rotm(q_obj_)*self.Rc

            return np.concatenate((np.concatenate((jac_q_obj,jac_q_config),axis=1),np.concatenate((jac_p_obj,jac_p_config),axis=1)),axis=0)

        def env_constraints(x):
            dp_obj = x[0:3]
            dq_obj = x[3:7]/np.linalg.norm(x[3:7])
            dp_config=x[7:10]
            dq_config=x[10:14]/np.linalg.norm(x[10:14])

            R_obj = np.dot(quat2rotm(dq_obj),quat2rotm(q_obj_))
            eqn1 = np.sum((np.dot(R_obj,self.envc1) + p_obj_ + dp_obj - self.envc1_w)**2)
            eqn2 = np.sum((np.dot(R_obj,self.envc2) + p_obj_ + dp_obj - self.envc2_w)**2)

            return np.concatenate(([eqn1],[eqn2]))

        def jac_env(x):
            dp_obj = x[0:3]
            dq_obj = x[3:7]/np.linalg.norm(x[3:7])
            dp_config=x[7:10]
            dq_config=x[10:14]
            qx = dq_config/np.linalg.norm(dq_config)

            jac1 = np.zeros([3,7])
            jac1[0:3,0:3] = np.identity(3)
            x = np.dot(quat2rotm(q_obj_),self.envc1)
            jac1[:,3:] =[[ 2*dq_config[2]*x[2] - 2*dq_config[3]*x[1],           2*dq_config[2]*x[1] + 2*dq_config[3]*x[2], 2*dq_config[0]*x[2] + 2*dq_config[1]*x[1] - 4*dq_config[2]*x[0], 2*dq_config[1]*x[2] - 2*dq_config[0]*x[1] - 4*dq_config[3]*x[0]],\
            [ 2*dq_config[3]*x[0] - 2*dq_config[1]*x[2], 2*dq_config[2]*x[0] - 4*dq_config[1]*x[1] - 2*dq_config[0]*x[2],           2*dq_config[1]*x[0] + 2*dq_config[3]*x[2], 2*dq_config[0]*x[0] + 2*dq_config[2]*x[2] - 4*dq_config[3]*x[1]],\
            [ 2*dq_config[1]*x[1] - 2*dq_config[2]*x[0], 2*dq_config[0]*x[1] - 4*dq_config[1]*x[2] + 2*dq_config[3]*x[0], 2*dq_config[3]*x[1] - 4*dq_config[2]*x[2] - 2*dq_config[0]*x[0],           2*dq_config[1]*x[0] + 2*dq_config[2]*x[1]]]

            jac2 = np.zeros([3,7])
            jac2[0:3,0:3] = np.identity(3)
            x = np.dot(quat2rotm(q_obj_),self.envc2)
            jac2[:,3:] =[[ 2*dq_config[2]*x[2] - 2*dq_config[3]*x[1],           2*dq_config[2]*x[1] + 2*dq_config[3]*x[2], 2*dq_config[0]*x[2] + 2*dq_config[1]*x[1] - 4*dq_config[2]*x[0], 2*dq_config[1]*x[2] - 2*dq_config[0]*x[1] - 4*dq_config[3]*x[0]],\
            [ 2*dq_config[3]*x[0] - 2*dq_config[1]*x[2], 2*dq_config[2]*x[0] - 4*dq_config[1]*x[1] - 2*dq_config[0]*x[2],           2*dq_config[1]*x[0] + 2*dq_config[3]*x[2], 2*dq_config[0]*x[0] + 2*dq_config[2]*x[2] - 4*dq_config[3]*x[1]],\
            [ 2*dq_config[1]*x[1] - 2*dq_config[2]*x[0], 2*dq_config[0]*x[1] - 4*dq_config[1]*x[2] + 2*dq_config[3]*x[0], 2*dq_config[3]*x[1] - 4*dq_config[2]*x[2] - 2*dq_config[0]*x[0],           2*dq_config[1]*x[0] + 2*dq_config[2]*x[1]]]

            jac = np.zeros([6,14])
            jac[0:3,0:7] = jac1
            jac[3:,0:7] = jac2

            R_obj = np.dot(quat2rotm(dq_obj),quat2rotm(q_obj_))
            d1 = 2*(np.dot(R_obj,self.envc1) + p_obj_ + dp_obj - self.envc1_w).reshape(1,-1)
            d2 = 2*(np.dot(R_obj,self.envc2) + p_obj_ + dp_obj - self.envc2_w).reshape(1,-1)

            jac = np.zeros([2,14])
            jac[0,0:7] = np.dot(d1,jac1)
            jac[1:,0:7] = np.dot(d2,jac2)


            return jac

        def energy(x):
            dp_obj = x[0:3]
            dq_obj = x[3:7]/np.linalg.norm(x[3:7])
            dp_config=x[7:10]
            dq_config=x[10:14]/np.linalg.norm(x[10:14])

            # f_config_: previous wrench on the robot rigid end
            x_ = np.concatenate((dp_config,quat2exp(dq_config)))
            #energy = self.obj_m*self.gravity*dp_obj[2] + 0.5*np.dot(x_,np.dot(K_spring,x_)) + np.dot(f_spring,x_)
            energy = 10*np.linalg.norm(dp_obj[0:3]) + 5*np.linalg.norm(np.dot(dq_obj,[1,0,0,0])-1) + 100*np.linalg.norm(dp_config[0:3]) + 10000*np.linalg.norm(np.dot(dq_config,[1,0,0,0])-1)
            return energy

        def jac_energy(x):
            dp_obj = x[0:3]
            dq_obj = x[3:7]/np.linalg.norm(x[3:7])
            dp_config=x[7:10]
            dq_config=x[10:14]
            x_ = np.concatenate((dp_config, quat2exp(dq_config)))

            jac_obj = np.zeros(7)
            jac_obj[2] = self.obj_m*self.gravity

            jac_x_ = np.zeros([6,7])
            jac_x_[0:3,0:3] = np.identity(3)
            qx = dq_config/np.linalg.norm(dq_config)
            if qx[0] == 1 or qx[0] == -1:
                jac_x_[3:, 4:] = np.identity(3)*50
            else:
                jac_x_[3:,3:] = np.array([[(qx[0]*qx[1])/(1 - qx[0]**2)**(3/2), 1/(1 - qx[0]**2)**(1/2),0,0],\
                [(qx[0]*qx[2])/(1 - qx[0]**2)**(3/2),0, 1/(1 - qx[0]**2)**(1/2),0],\
                [(qx[0]*qx[3])/(1 - qx[0]**2)**(3/2),0,0, 1/(1 - qx[0]**2)**(1/2)]])

            jac_config = np.squeeze((np.dot(K_spring,x_) + f_config_.T).dot(jac_x_).reshape(1,-1))
            jac = np.concatenate((jac_obj,jac_config))
            return jac

        np.random.seed(0)
        x0 = np.zeros(14)
        x0[0:3] = np.random.rand(3)/10
        x0[7:10] = np.random.rand(3)/10
        x0[3:7]= np.array([1,0,0,0])
        x0[10:14] =np.array([1,0,0,0])
        #bnds = ((-10,10),(-10,10),(-20,20),(None,None),(None,None),(None,None),(None,None),(-10,10),(-10,10),(-20,20),(None,None),(None,None),(None,None),(None,None))
        #cons=({"type":"eq","fun":env_constraints,"jac":jac_env},{"type":"eq","fun":robot_constraints,"jac":jac_robot})
        #print('x0', x0)
        #print('env_constraints x0', env_constraints(x0))
        #print('robot_constraints x0', robot_constraints(x0))
        '''
        cons = ({"type": "eq", "fun": env_constraints,"jac":jac_env},
                {"type": "eq", "fun": robot_constraints,"jac":jac_robot},
                {"type": "eq", "fun": lambda x:  1 - np.linalg.norm(x[3:7])},
                {"type": "eq", "fun": lambda x:  1 - np.linalg.norm(x[10:14])})
                '''
        cons = ({"type": "eq", "fun": env_constraints},
                {"type": "eq", "fun": robot_constraints},
                {"type": "eq", "fun": lambda x:  p_obj_[2] + x[2] - self.obj_lz},
                {"type": "eq", "fun": lambda x:  1 - np.dot(x[3:7],x[3:7])},
                {"type": "eq", "fun": lambda x:  1 - np.dot(x[10:14],x[10:14])})
        #res = minimize(energy, x0, method='SLSQP', tol=1e-4, jac=jac_energy, constraints=cons)
        opts = {'maxiter':500}
        res = minimize(energy, x0,tol=0.04, constraints=cons,  options=opts)#, bounds = bnds)
        print('status:',res.status,', iter:',res.nit, ', fun:',res.fun)
        #print('x res', x0)
        print('env_constraints res', env_constraints(res.x))
        print('robot_constraints res', robot_constraints(res.x))
        if res.status == 0:
            return_x = res.x
        else:
            return_x = np.zeros(14)
            return_x[3:7]= np.array([1,0,0,0])
            return_x[10:14] =np.array([1,0,0,0])
        return return_x

    def actual2robot(self,p_actual_):
        # convert actual robot position to execute to robot rigid end position
        p_actual = np.copy(p_actual_)
        p_rigid = np.zeros(7)
        p_rigid[3:] = p_actual[3:]
        p_rigid[0:3] = p_actual[0:3] + np.dot(quat2rotm(p_actual[3:]),[0,0,self.gripper_length]) - self.origin.reshape(-1)
        return p_rigid

    def robot2actual(self, p_rigid_):
        p_rigid = np.copy(p_rigid_)
    	p_actual = np.zeros(7)
    	p_actual[3:]=p_rigid[3:]
    	p_actual[0:3] = p_rigid[0:3] - np.dot(quat2rotm(p_rigid[3:]),[0,0,self.gripper_length]) + self.origin.reshape(-1)
    	return p_actual

def to_poseStamped(t, seqid, p, q):
    poses = PoseStamped()
    poses.header.stamp = t
    poses.header.seq = seqid
    poses.header.frame_id = 'world'
    poses.pose.position.x = p[0]
    poses.pose.position.y = p[1]
    poses.pose.position.z = p[2]
    poses.pose.orientation.x = q[1]
    poses.pose.orientation.y = q[2]
    poses.pose.orientation.z = q[3]
    poses.pose.orientation.w = q[0]

    return poses

def to_wrenchStamped(t, seqid, f):
    wrenchs = WrenchStamped()
    wrenchs.header.stamp = t
    wrenchs.header.seq = seqid
    wrenchs.header.frame_id = 'tool'
    wrenchs.wrench.force.x = f[0]
    wrenchs.wrench.force.y = f[1]
    wrenchs.wrench.force.z = f[2]
    wrenchs.wrench.torque.x = f[3]
    wrenchs.wrench.torque.y = f[4]
    wrenchs.wrench.torque.z = f[5]

    return wrenchs

